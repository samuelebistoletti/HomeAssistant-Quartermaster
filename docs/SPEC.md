# Quartermaster — specifica tecnica di implementazione

Quartermaster è un'integrazione custom per Home Assistant, distribuita tramite il Marketplace (HACS), che aggiunge un agente AI per gestire dispensa, frigorifero, freezer e scaffali e compila da sola la lista della spesa. L'inventario vive in un archivio interno all'integrazione. L'agente si usa a voce con Alexa e Assist, da Telegram, dall'app di Home Assistant su iOS e Android e dalle automazioni.

Questo documento è la specifica di riferimento da consegnare a Claude Code: descrive cosa costruire, con quali componenti e in quale ordine.

| Voce | Valore |
| --- | --- |
| Nome e dominio | Quartermaster, dominio quartermaster |
| Repository dell'integrazione | github.com/samuelebistoletti/HomeAssistant-Quartermaster |
| Repository della dashboard | github.com/samuelebistoletti/HomeAssistant-Quartermaster-Dashboard |
| Licenza | MIT |
| Lingue | Codice, commenti, README e interfaccia in inglese; traduzione italiana completa |
| Piattaforme | Ogni tipo di installazione di Home Assistant (OS, Supervised, Container, Core); stessa esperienza su iOS, Android, web e voce |

## 1. Obiettivi e ambito

Obiettivi:

- Tenere aggiornato l'inventario di casa per zone, soprattutto con foto scattate dall'utente o prese dalle telecamere, senza scansione obbligatoria dei codici a barre.
- Usare la telecamera di una zona quando c'è e chiedere una foto all'utente solo quando serve.
- Registrare i consumi con il minimo sforzo: una frase ad Alexa o ad Assist, un messaggio su Telegram, o il prodotto mostrato alla telecamera della zona.
- Gestire una lista di prodotti essenziali, che devono esserci sempre, generare da essa la lista della spesa e suggerire acquisti durante la spesa in base a quanto c'è in casa.
- Funzionare con qualsiasi provider AI configurato in Home Assistant, compresi i modelli locali.
- Nessuna dipendenza esterna obbligatoria: basta installare l'integrazione.
- Qualità adatta al catalogo predefinito del Marketplace e al livello Bronze della quality scale.

Fuori ambito per la versione 1.0:

- Ricette e pianificazione dei pasti.
- Adattatori verso archivi esterni (Grocy, EverShelf): l'architettura li prevede tramite l'interfaccia di archivio.
- Hardware dedicato (bilance, camere interne): previsto come nuovi tipi di fonte.
- App mobile nativa: l'interfaccia passa dall'app di Home Assistant, da Telegram e da Alexa.

## 2. Scelte architetturali

| Id | Scelta | Motivo |
| --- | --- | --- |
| S1 | Integrazione custom in custom\_components/quartermaster, distribuita dal Marketplace | Funziona su ogni tipo di installazione |
| S2 | Archivio interno in SQLite (modulo sqlite3 della libreria standard) nella cartella di configurazione | Nessuna dipendenza aggiuntiva, storico dei movimenti, incluso nei backup di Home Assistant |
| S3 | Interfaccia di archivio astratta (StorageBackend) | Adattatori futuri senza toccare agente ed entità |
| S4 | Modelli tramite AI Task (ai\_task.generate\_data) per immagini e compiti strutturati | L'utente sceglie qualsiasi provider già configurato; output strutturato e allegati supportati |
| S5 | Conversazione tramite l'agente scelto dall'utente, a cui l'integrazione aggiunge strumenti con la LLM API (llm.py) | Un solo agente per Assist, Telegram, Alexa e app; nessun client verso i provider |
| S6 | Telegram tramite l'integrazione Telegram bot di Home Assistant | Nessun secondo client; elenco delle chat consentite già gestito da Home Assistant |
| S7 | Alexa tramite la custom skill nativa di Home Assistant e l'azione generica quartermaster.ask chiamata da un intent\_script | Nessun codice Alexa nell'integrazione; quartermaster.ask conserva il filo della conversazione e indica quando tenere aperto il microfono |
| S8 | Lista della spesa come entità todo dell'integrazione, con copia facoltativa verso un'altra entità todo | Visibile e modificabile nell'app di Home Assistant; chi vuole la vede su Bring!, Todoist, KitchenOwl o Mealie |
| S9 | Prodotti essenziali con scorta minima e obiettivo; lista a tre livelli: da comprare, suggeriti, a posto | Lista di cose che devono esserci sempre, con suggerimenti durante la spesa |
| S10 | Zone con fonti in ordine di priorità: telecamera, poi foto dell'utente | Requisito centrale del progetto |
| S11 | Prelievo mostrato alla telecamera come fonte facoltativa per zona (camera\_show), disattivata per impostazione predefinita | Consumi registrati senza telefono; facoltativa perché i fotogrammi riprendono persone |
| S12 | Variazioni da foto con conferma; modalità automatica sopra una soglia di affidabilità a scelta dell'utente | Gli errori di riconoscimento non sporcano i dati |
| S13 | Dashboard generata da una strategia di dashboard, con card native e poche card personalizzate, distribuita da un secondo repository di tipo dashboard | Unico meccanismo documentato per portare JavaScript nel frontend; il Marketplace registra la risorsa da solo |
| S14 | Nessuna dipendenza Python oltre a quelle già incluse in Home Assistant (Pillow compreso) | Installazione senza problemi di compatibilità |

Principio guida: l'integrazione contiene la logica di dominio (inventario, zone, scorte, lista, suggerimenti) e delega a Home Assistant tutto ciò che Home Assistant sa già fare (modelli, chat, voce, telecamere, notifiche).

## 3. Architettura

```
Utente:  Alexa (Echo)   Assist (app, Voice PE)   Telegram          Dashboard (web, iOS, Android)
            |                 |                     |                    |
     alexa + intent_script    |              telegram_bot (HA)      card + websocket
            |                 |                     |                    |
     quartermaster.ask -----> agente di conversazione (provider scelto) + strumenti LLM
            |                 |                     |                    |
+-----------v-----------------v---------------------v--------------------v-----------+
|                          Integrazione quartermaster                                |
|  channels/  telegram · websocket                                                   |
|  core/      inventory · zones · shopping · suggestions · confirmations · voice     |
|  vision/    acquisizione · qualità · ritaglio · show · ai_task.generate_data       |
|  storage/   StorageBackend → SQLite                                                |
|  entità     todo · sensor · binary_sensor · button · select · switch · calendar    |
|  notify     instradamento verso le entità notify scelte, silenzio notturno         |
+-----------------|------------------------|-----------------------|----------------+
                  |                        |                       |
        entità camera (qualsiasi)   entità ai_task (qualsiasi)   entità notify (qualsiasi)
```

### 3.1 Componenti di Home Assistant usati

| Esigenza | Componente | Uso |
| --- | --- | --- |
| Analisi immagini | AI Task, azione ai\_task.generate\_data | Istruzioni, schema di output (structure) e immagini come allegati media-source |
| Conversazione | Agente di conversazione dell'utente, azione conversation.process | Testi da Telegram, Alexa e dashboard inoltrati all'agente con un conversation\_id per canale |
| Strumenti per l'agente | LLM API, piattaforma llm.py | Strumenti di inventario, lista e suggerimenti per gli agenti che usano l'API Assist |
| Voce | Assist; integrazioni alexa e intent\_script | Assist usa direttamente gli strumenti; Alexa passa da quartermaster.ask |
| Chat | Integrazione Telegram bot | Eventi dei messaggi in arrivo, download degli allegati, messaggi con pulsanti |
| Telecamere | Entità camera; entità evento o binary\_sensor di movimento | Fotogrammi come allegati media-source://camera/\<entity\_id>; innesco di camera\_show |
| Notifiche | Entità notify, azione notify.send\_message | App mobile, Echo (per esempio con Alexa Devices), Telegram o altro, a scelta |
| Lista della spesa | Piattaforma todo | Entità todo propria, più copia facoltativa su un'entità todo esterna |
| File | Media source e cartella media | Foto temporanee in una sottocartella della cartella media per allegarle ad AI Task |
| Frontend | Strategia di dashboard e card personalizzate (repository dashboard) | Dashboard dedicata |

### 3.2 Moduli dell'integrazione

| Modulo | Responsabilità |
| --- | --- |
| `__init__.py`, config\_flow.py | Setup, migrazioni, config flow e options flow con selettori di entità |
| storage/ | Interfaccia StorageBackend, implementazione SQLite, migrazioni di schema; accesso solo tramite executor |
| core/inventory.py | Prodotti, quantità per zona, movimenti (acquisto, consumo, inventario, correzione, annullamento), alias |
| core/zones.py | Zone, fonti in priorità, controlli programmati, promemoria |
| core/shopping.py | Lista della spesa, modalità spesa, copia verso todo esterna |
| core/suggestions.py | Essenziali, calcolo di da comprare e suggeriti con il motivo |
| core/confirmations.py | Proposte di variazione in attesa, conferma o rifiuto da qualsiasi canale |
| core/voice.py | Azione quartermaster.ask: sessioni per canale con scadenza, chiarimenti in sospeso, presa in carico oltre 5 secondi |
| vision/ (acquire, quality, phash, analyze, show, schema) | Acquisizione, controlli di qualità, ritaglio, hash percettivo, chiamate AI Task, validazione, fonte camera\_show |
| llm.py | Strumenti LLM e frammento di prompt |
| channels/telegram.py | Eventi del Telegram bot: testi, foto, comandi, pulsanti |
| channels/websocket.py | Comandi websocket per le card della dashboard, con controllo dei permessi |
| notify.py | Instradamento delle notifiche, silenzio notturno, ripiego |
| todo.py, sensor.py, binary\_sensor.py, button.py, select.py, switch.py, calendar.py | Entità |
| services.py, services.yaml | Azioni dell'integrazione |
| diagnostics.py | Diagnostica con dati sensibili oscurati |

## 4. Modello dati

File SQLite: \<config>/quartermaster/pantry.db. Accesso solo tramite l'executor di Home Assistant, modalità WAL, migrazioni numerate in storage/migrations.

| Tabella | Campi | Note |
| --- | --- | --- |
| zones | id, name, aliases, sources (JSON), check\_schedule, reminder\_days, confidence\_threshold | Modificabili da options flow e dashboard |
| products | id, name, aliases, category, default\_zone\_id, unit, essential, min\_stock, target\_stock, visual\_hints, barcode, shelf\_life\_days, archived | essential indica i prodotti che devono esserci sempre; min\_stock obbligatoria per gli essenziali; target\_stock quantità a cui riportarli; barcode facoltativo |
| stock | product\_id, zone\_id, quantity, best\_before, opened\_at, updated\_at | Una riga per prodotto e zona |
| journal | id, ts, product\_id, zone\_id, kind (purchase, consume, inventory, correction, undo, import), delta, quantity\_after, channel (alexa, assist, telegram, dashboard, camera, service, scheduler), user\_id, confidence, image\_ref, undoes\_id | Storico completo e base per l'annullamento |
| shopping\_items | id, product\_id, text, quantity, status, origin (auto, suggestion, manual), external\_uid | Le voci manuali non vengono mai rimosse in automatico |
| pending\_changes | id, created, channel, zone\_id, payload (JSON), expires\_at, status | Proposte in attesa di conferma |
| zone\_snapshots | zone\_id, ts, perceptual\_hash, overall\_confidence, result (JSON) | Ultimo risultato per zona, per saltare immagini invariate |
| settings | key, value | Versione dello schema e preferenze interne |

Regole:

- Quantità decimali con unità per prodotto; nessuna conversione tra unità nella 1.0.
- Ogni scrittura passa dal servizio di inventario, che registra il movimento nel journal e aggiorna entità, lista e suggerimenti in un'unica transazione.
- Esportazione e importazione in JSON e CSV tramite azioni, per backup e migrazione.
- Importazione da qualsiasi entità todo esistente (per esempio una lista Bring! usata come inventario o come elenco degli essenziali), con anteprima e conferma.

### 4.1 Zone

Le zone si creano dall'options flow o dalla dashboard e si salvano nell'archivio.

```yaml
- id: garage
  name: Scaffale garage
  aliases: [scaffale, garage]
  sources:
    - type: camera
      entity_id: camera.garage
      roi: [0.62, 0.05, 1.0, 1.0]   # ritaglio x1,y1,x2,y2 in frazioni
      max_age_minutes: 30
      max_checks_per_day: 2         # utile per telecamere a batteria
    - type: camera_show             # facoltativa, sezione 5.1
      entity_id: camera.garage
      trigger_entity: event.garage_motion
      roi: [0.30, 0.10, 0.80, 0.90]
    - type: manual
  check_schedule: "0 19 * * *"
  confidence_threshold: 0.75
- id: fridge
  name: Frigorifero
  aliases: [frigo, frigorifero]
  sources: [{type: manual}]
  reminder_days: 3
- id: scorte
  name: Mobile delle scorte
  sources:
    - type: ledger                  # controllo a elenco, sezione 5.2
      check_days: 30                # 0 = solo su richiesta
```

I tipi di fonte sono estensibili (per esempio sensor per una bilancia ESPHome) senza cambiare il resto del sistema.

### 4.2 Output dell'analisi di una zona

ai\_task.generate\_data riceve uno schema di output (structure) con questi campi; l'integrazione valida il risultato e scarta ciò che non è coerente.

```json
{
  "image_usable": true,
  "image_issues": ["riflessi"],
  "items": [
    {"product": "Sugo di pomodoro", "matched_product_id": 12,
     "quantity": 2, "unit": "bottiglia", "confidence": 0.86,
     "notes": "terza bottiglia forse dietro"}
  ],
  "expected_not_seen": ["Pasta mezze maniche"],
  "unknown_items": ["barattolo senza etichetta"],
  "overall_confidence": 0.71
}
```

Per i provider che gestiscono male le strutture annidate esiste una modalità di ripiego: un campo di testo con JSON validato dall'integrazione.

## 5. Acquisizione per zona

Ogni controllo di zona (programmato, chiesto a voce, da Telegram, dalla dashboard, da un pulsante o da un'azione) segue lo stesso algoritmo:

1. Scorrere le fonti della zona in ordine di priorità (la fonte camera\_show non partecipa: ha un flusso proprio, sezione 5.1).
2. Fonte camera: leggere il fotogramma dall'entità camera. Scartarlo se la lettura fallisce o supera 15 secondi, se è più vecchio di max\_age\_minutes, se dopo il ritaglio il lato corto è sotto 640 px, se è troppo scuro o sfocato (luminosità media e varianza del laplaciano calcolate con Pillow), o se è stato superato max\_checks\_per\_day.
3. Calcolare un hash percettivo (dHash con Pillow): se l'immagine è quasi identica all'ultima analizzata, riusare il risultato precedente senza chiamare il modello.
4. Salvare il ritaglio nella cartella media dell'integrazione e chiamare ai\_task.generate\_data con istruzioni, prodotti attesi nella zona, schema di output e immagine come allegato.
5. Se overall\_confidence è sotto la soglia della zona o un essenziale atteso non è stato visto, il risultato è insufficiente: passare alla fonte successiva.
6. Fonte ledger: nessuna foto; si invia l'elenco della zona per il controllo guidato (sezione 5.2).
7. Fonte manual: inviare all'utente una richiesta di foto con il motivo (telecamera non raggiungibile, immagine poco nitida, prodotti non riconosciuti) e cosa inquadrare, sul canale da cui è partita la richiesta o su quello preferito. La richiesta compare anche nella dashboard e nel sensore Foto richieste.
8. Una richiesta senza risposta riceve un solo promemoria dopo 12 ore, poi scade; la zona mantiene i dati precedenti.
9. Nelle zone senza telecamera (e non tenute a registro) lo scheduler invia un promemoria ogni reminder\_days dall'ultimo aggiornamento, rispettando il silenzio notturno.
10. Con un risultato valido il servizio di inventario prepara le variazioni. Per le foto inviate dall'utente: in modalità conferma crea una proposta in attesa; in modalità automatica applica le variazioni sopra soglia e propone le altre. Per le telecamere: sempre una proposta, e solo delle diminuzioni; i prodotti visti in quantità maggiore di quella registrata vengono segnalati, non caricati.

Prodotti attesi e non visti (decisione dell'autore, 2026-11-11): un prodotto registrato nella zona che il modello non vede (expected\_not\_seen, riconosciuto per nome o alias) entra nella proposta come riga «non visto» con quantità zero. Non viene mai applicato in automatico e la conferma della proposta intera lo esclude, perché il prodotto può essere solo nascosto: lo si azzera con il suo pulsante su Telegram («<prodotto> finito»), spuntandolo nella dashboard o indicandone la chiave a voce. Se non lo si tocca, la riga scade con la proposta.

Regola di carico (decisione dell'autore, 2026-11-11): la merce entra con uno scontrino confermato (foto o email d'ordine) o con una foto inviata di proposito dall'utente, che mostra lo scaffale com'è e può quindi anche aumentare le quantità (regali, acquisti senza scontrino). Una telecamera non carica mai e non scrive mai senza una conferma dell'utente.

Le foto dell'utente seguono la stessa pipeline da tre ingressi, tutti disponibili su iOS e Android: Telegram, caricamento dalla dashboard e azione quartermaster.analyze\_image con un'immagine già presente in Home Assistant.

Riservatezza: al modello va solo il ritaglio della zona; le foto temporanee si cancellano dopo l'analisi, salvo un'opzione di conservazione per il debug; nessuna immagine nei log o nella diagnostica.

### 5.1 Prelievo mostrato alla telecamera (fonte camera\_show)

Nelle zone con telecamera, chi prende un prodotto lo mostra all'obiettivo per un paio di secondi; l'agente lo riconosce e registra il consumo. La fonte è facoltativa, disattivata per impostazione predefinita e configurata per zona.

```yaml
- type: camera_show
  entity_id: camera.garage              # stessa telecamera della zona o un'altra
  trigger_entity: event.garage_motion   # entità evento o binary_sensor di movimento
  roi: [0.30, 0.10, 0.80, 0.90]         # area in cui mostrare il prodotto
  capture: auto                         # auto | burst | clip
  window_seconds: 6                     # durata della cattura dopo il movimento
  burst_interval_seconds: 0.5           # intervallo tra fotogrammi in modalità burst
  max_frames_to_model: 4                # fotogrammi migliori inviati al modello
  cooldown_seconds: 30
  max_per_day: 40
  active: when_armed                    # always | when_armed
  min_confidence: 0.8
  hours: "07:00-23:00"                  # facoltativo
```

Flusso:

1. Innesco: un nuovo evento dell'entità evento o il passaggio a on del binary\_sensor. Con active: when\_armed (predefinito) la fonte reagisce solo se l'interruttore Modalità mostra prodotto della zona è acceso; l'interruttore si spegne da solo dopo un tempo configurabile (predefinito 10 minuti) e si accende dalla dashboard, da un'automazione o a voce ("sto prendendo delle cose dal garage", strumento QuartermasterArmShowMode).
2. Controllo di fascia oraria, cooldown e limite giornaliero.
3. Cattura per window\_seconds dopo il movimento. Modalità clip: breve registrazione mp4 con l'azione camera.record (richiede l'integrazione stream e una telecamera che la supporti) e decodifica dei fotogrammi. Modalità burst: letture ripetute dell'immagine della telecamera ogni burst\_interval\_seconds, valida per qualsiasi telecamera. Con auto si usa clip se disponibile, altrimenti burst. Su ogni fotogramma: ritaglio dell'area di presentazione, punteggio di nitidezza (varianza del laplaciano) e di cambiamento rispetto a un fotogramma di riferimento della scena vuota, scarto dei duplicati con hash percettivo. Se nessun fotogramma mostra un cambiamento significativo nell'area (per esempio un'auto che entra in garage) il flusso si ferma senza chiamare il modello. Altrimenti si scelgono fino a max\_frames\_to\_model fotogrammi, i più nitidi e distribuiti nel tempo, in ordine cronologico.
4. Chiamata ad ai\_task.generate\_data con i fotogrammi scelti come allegati in ordine cronologico (se il provider ne accetta uno solo, il più nitido), i prodotti attesi nella zona e lo schema qui sotto: il modello indica prodotto, quantità e azione (preso, rimesso, non chiaro). Al modello vanno solo immagini: l'invio di video non è documentato per AI Task. Le istruzioni chiedono di ignorare le persone e di non descriverle.
5. Nessun prodotto mostrato: nessuna azione, non conta come errore.
6. Prodotto riconosciuto: nessuna scrittura, ma una richiesta all'utente. Sopra min\_confidence arriva su Telegram e nelle notifiche ("Scaffale garage: hai preso 1 passata? Ne avevi 4, ne resterebbero 3") con i pulsanti per la quantità vista, una in più, tutte, e No; sotto soglia resta in attesa nella dashboard senza messaggio, finché una telecamera di passaggio non vede lo stesso prodotto uscire. Alla conferma il consumo si registra (canale di chi conferma), arriva l'esito con il pulsante Annulla e, se scelto, l'annuncio sull'Echo della zona ("Ho segnato una passata di pomodoro in meno"). Senza risposta la richiesta scade e non cambia nulla.
7. Se il prodotto è essenziale e scende sotto la scorta minima entra in lista, e la stessa notifica lo dice.
8. Se i fotogrammi restano invariati per tre inneschi di fila, la fonte si sospende e crea una segnalazione nelle riparazioni.

```json
{
  "product_shown": true,
  "action": "taken",
  "product": "Passata di pomodoro",
  "matched_product_id": 12,
  "quantity": 1,
  "confidence": 0.91,
  "best_frame_index": 2,
  "notes": "etichetta leggibile"
}
```

action vale taken (richiesta di confermare il consumo), returned (il prodotto viene rimesso: se la richiesta del prelievo è ancora in attesa si annullano a vicenda; se il consumo era già confermato si chiede di annullarlo; altrimenti non si registra nulla, perché la merce entra con lo scontrino o con una foto, e l'utente viene avvisato) o unclear (nessuna scrittura, proposta in attesa con il fotogramma migliore).

Le telecamere di passaggio non scrivono: confermano la confidenza di un avvistamento della zona, chiedono conferma per la merce portata fuori, e per la merce portata dentro non fanno nulla.

camera\_show integra le altre fonti: l'inventario della zona si aggiorna comunque con i controlli dell'algoritmo precedente.

### 5.2 Controllo a elenco (fonte ledger)

Vale per qualsiasi zona che una foto mostra male o che non si vede facilmente: un mobile con le scorte dietro la prima fila, il frigorifero pieno, un freezer. Le variazioni arrivano come sempre: scontrini, foto inviate di proposito, frasi dette o scritte ("ho messo due vasetti di marmellata nel mobile", "ho finito i piselli"). Si sceglie nel setup guidato ("Ripassiamo l'elenco") o con la fonte ledger. Una zona con questa fonte:

1. Non chiede mai foto, non riceve promemoria di foto e non genera il suggerimento "zona non aggiornata". Una foto inviata di proposito resta accettata.
2. Ogni check\_days (predefinito 30; 0 = solo su richiesta), oppure con /controlla &lt;zona&gt; su Telegram, il pulsante di controllo, l'azione check\_zone o a voce, riceve un controllo guidato: l'elenco della zona dal più vecchio (per scadenza, poi per ultima variazione), con i pulsanti "−1", "finito" e "Tutto giusto". Si può rispondere anche a parole. A voce l'agente legge i cinque prodotti più vecchi.
3. Dopo una fonte camera, l'elenco sostituisce la richiesta di foto quando il fotogramma non basta, e non c'è l'invio periodico.
4. Per i prodotti a lunga conservazione (surgelati, conserve) si usa shelf\_life\_days: un acquisto senza data riceve la data di oggi più quella durata, e calendario e messaggio delle scadenze dicono cosa consumare.

Riservatezza: il config flow spiega che i fotogrammi riprendono persone prima di attivare la fonte. Al provider va solo l'area di presentazione; i fotogrammi si cancellano subito dopo l'analisi anche con l'opzione di debug attiva e non compaiono mai in log, diagnostica o notifiche, dove va solo il nome del prodotto.

## 6. Agente, canali e interfacce

### 6.1 Strumenti LLM

La piattaforma llm.py aggiunge questi strumenti agli agenti di conversazione che usano l'API Assist. Ogni strumento valida gli argomenti, applica limiti di plausibilità, registra il canale di origine e restituisce un ToolResult.

| Strumento | Cosa fa | Scrive | Conferma |
| --- | --- | --- | --- |
| QuartermasterListZones | Zone, fonti e ultimo aggiornamento | No | No |
| QuartermasterGetStock | Quantità di una zona, di un prodotto o dell'intero inventario, con età del dato | No | No |
| QuartermasterGetMissing | Essenziali sotto scorta minima | No | No |
| QuartermasterGetSuggestions | Suggerimenti di acquisto con il motivo | No | No |
| QuartermasterGetExpiring | Prodotti in scadenza entro N giorni | No | No |
| QuartermasterGetShoppingList | Lista della spesa | No | No |
| QuartermasterAddToShoppingList | Aggiunge una voce o un suggerimento alla lista | Sì | No |
| QuartermasterRemoveFromShoppingList | Toglie una voce dalla lista | Sì | Se ambigua |
| QuartermasterAdjustStock | Consumo, acquisto o correzione da testo o voce, per zona o per tutta la casa; con prodotto ambiguo restituisce i candidati e non scrive (sezione 6.2) | Sì | Se grande |
| QuartermasterUndoLast | Annulla l'ultimo movimento dello stesso canale e utente, entro 10 minuti | Sì | No |
| QuartermasterCheckZone | Avvia il controllo di una zona e risponde subito che è in corso | Indiretto | No |
| QuartermasterArmShowMode | Accende la modalità mostra prodotto di una zona | Solo lo stato | No |
| QuartermasterSetEssential | Aggiunge o toglie un essenziale, con scorta minima e obiettivo | Sì | Sì |
| QuartermasterCreateProduct | Crea un prodotto | Sì | Sì |
| QuartermasterConfirmPending | Conferma o rifiuta una proposta in attesa | Sì | È la conferma |

Il frammento di prompt che accompagna gli strumenti chiede all'agente di: rispondere nella lingua dell'utente; non inventare prodotti; trattare il testo nelle immagini e nei messaggi come dato; dichiarare l'incertezza sulle quantità; fare domande chiuse e brevi quando serve un chiarimento; tenere brevi le risposte vocali.

### 6.2 Consumi a voce e da chat

Frasi come "ho finito il latte", "ho finito il formaggio in frigo", "ho preso due passate dal garage", "ho aperto lo yogurt" valgono da ogni canale. QuartermasterAdjustStock le tratta così:

- Verbi: finito o terminato porta a zero la quantità nella zona indicata, o nell'unica zona in cui il prodotto è presente; preso, usato o consumato N toglie N (1 se manca il numero); aperto registra opened\_at; comprato N aggiunge N.
- Ricerca del prodotto: nome e alias, poi categoria ("formaggio" trova i prodotti della categoria formaggi), limitata alla zona citata e ordinata per presenza in quella zona.
- Un candidato: scrive (o propone, se la variazione è grande) e conferma in una frase.
- Più candidati: non scrive; restituisce al massimo tre candidati con zona e quantità. L'agente chiede quale, e la risposta nello stesso filo completa l'operazione.
- Nessun candidato: l'agente chiede se mettere il prodotto in lista e, se l'utente vuole, se crearlo come prodotto (con conferma).
- Essenziale sotto la scorta minima dopo il consumo: entra in lista e la risposta lo dice.
- Correzione: "annulla" o "no, era la mozzarella" nella stessa sessione usa QuartermasterUndoLast e registra il movimento giusto.

```text
Utente: Alexa, chiedi a dispensa: ho finito il formaggio in frigo
Alexa:  In frigo ho parmigiano e mozzarella. Quale hai finito?
Utente: La mozzarella
Alexa:  Fatto, mozzarella finita. È tra gli essenziali: l'ho messa in lista.

Utente: Alexa, chiedi a dispensa: ho finito il latte
Alexa:  Fatto, latte finito e messo in lista.
```

### 6.3 Canali

| Canale | Funzionamento | Piattaforme |
| --- | --- | --- |
| Alexa | Custom skill nativa di Home Assistant; l'intent\_script passa la frase a quartermaster.ask e legge la risposta (sezione 6.4) | Echo, app Alexa |
| Assist | L'utente parla o scrive ad Assist; l'agente usa direttamente gli strumenti | App di Home Assistant su iOS e Android, web, Voice PE, satelliti |
| Telegram | L'integrazione ascolta gli eventi del Telegram bot per le chat consentite: i testi vanno all'agente con un conversation\_id per chat, le foto alla pipeline di visione, le conferme usano pulsanti inline | iOS, Android, desktop |
| Dashboard | Strategia e card del repository dashboard (sezione 6.5); la chat usa Assist, già presente nell'app | Web e app di Home Assistant su iOS e Android, telefono e tablet |
| Entità e azioni | Lista todo, sensori, pulsanti, calendario, azioni per script e automazioni | Ovunque arrivi Home Assistant |

Compiti lunghi su canali vocali: se la risposta non è pronta entro 5 secondi, l'agente risponde con una presa in carico e l'esito arriva come notifica sul canale preferito.

### 6.4 Alexa

Alexa è il canale vocale principale per domande, consumi e lista. Si usa la custom skill nativa di Home Assistant (integrazioni alexa e intent\_script, Lambda di inoltro, collegamento dell'account con l'OAuth di Home Assistant). Quartermaster non contiene codice Alexa: fornisce l'azione quartermaster.ask, il modello di interazione, l'intent\_script di esempio e la guida docs/alexa.md.

Invocazione: ogni richiesta contiene il nome della skill scelto dall'utente. In italiano Alexa accetta queste forme, tutte supportate dal modello di interazione di esempio:

- in una frase sola: "Alexa, chiedi a dispensa se ho ancora il latte", "Alexa, domanda a dispensa cosa manca", "Alexa, di' a dispensa di segnare il latte finito", "Alexa, usa dispensa e segna il latte finito", "Alexa, apri dispensa e segna il latte finito" (anche avvia, lancia, accedi a, parla con, vai su);
- solo con il nome: "Alexa, dispensa" apre la skill, che risponde "Dimmi" e tiene aperto il microfono; poi si parla liberamente ("ho finito il latte").

L'interazione senza nome non è disponibile per questo tipo di skill in italiano; le routine di Alexa servono per frasi fisse ("Alexa, cosa manca in casa"). Nome di invocazione: "dispensa". Amazon non ammette nomi di una sola parola nella certificazione; la skill è privata e non va certificata, ma se la console non accetta il nome in sviluppo si usa "dispensa di casa" (verifica in Fase 0).

Azione quartermaster.ask:

- Ingresso: text; channel (alexa, voice o altro); language e session\_key facoltativi.
- Conserva per canale e chiave il conversation\_id dell'ultimo scambio e l'eventuale chiarimento in sospeso (i candidati di QuartermasterAdjustStock) per un tempo configurabile, predefinito 120 secondi: la risposta a una domanda continua lo stesso filo anche se arriva come nuova richiesta.
- Chiama conversation.process con l'agente scelto nelle opzioni e registra il canale nel journal.
- Se la risposta non è pronta entro 5 secondi restituisce una presa in carico e completa con una notifica (Alexa concede alla skill 8 secondi).
- Restituisce: speech, continue\_conversation (vero quando l'agente aspetta una risposta), reprompt (frase breve che ripete la domanda) e conversation\_id.

Esempio in examples/alexa/:

```yaml
# Modello di interazione: intent QuartermasterQuery con slot query
# di tipo AMAZON.SearchQuery e frase "{query}";
# intent AMAZON.StopIntent e AMAZON.CancelIntent.
intent_script:
  # "Alexa, dispensa": richiesta di avvio, identificata dall'ID della skill
  amzn1.ask.skill.<id-della-skill>:
    speech:
      text: "Dimmi"
    reprompt:
      text: "Cosa devo fare?"
  QuartermasterQuery:
    action:
      - action: quartermaster.ask
        data:
          text: "{{ query }}"
          channel: alexa
        response_variable: qm
      - stop: ""
        response_variable: qm
    speech:
      text: "{{ action_response.speech }}"
    reprompt:
      text: >-
        {{ action_response.reprompt
           if action_response.continue_conversation else '' }}
  AMAZON.StopIntent:
    speech:
      text: "Va bene"
```

Microfono: il reprompt è presente solo quando l'agente fa una domanda, quindi Alexa tiene aperto il microfono per la risposta; quando la risposta non chiede nulla il reprompt è vuoto e la sessione si chiude. Se la verifica pratica (sezione 12, Fase 0) mostra un comportamento diverso, l'esempio usa un reprompt fisso e breve ("Altro?").

Annunci: risultati lunghi, prelievi riconosciuti da camera\_show, essenziali finiti e promemoria arrivano sulle entità notify degli Echo scelte per tipo di messaggio e, dove ha senso, per zona.

Permessi: chiunque sia vicino a un Echo può parlare alla skill. Per il canale alexa sono consentiti letture, consumi, acquisti e modifiche della lista; creazione di prodotti, essenziali e variazioni grandi richiedono un "sì" esplicito nella stessa sessione. Un'opzione limita Alexa alla sola lettura. Tutto resta nel journal.

### 6.5 Dashboard

La dashboard è generata da una strategia di dashboard registrata in window.customStrategies, che compare nella finestra Nuova dashboard tra le dashboard della community, e si aggiorna da sola quando cambiano zone o prodotti. Usa card native dove bastano e card personalizzate dove servono. È pensata soprattutto per il telefono e funziona anche su tablet: vista a sezioni, una colonna su telefono e più colonne su tablet, controlli dimensionati per il tocco, tema chiaro e scuro tramite le variabili di tema.

| Vista | Contenuto | Interazione | Card |
| --- | --- | --- | --- |
| Panoramica | Da comprare, suggeriti, in scadenza, foto richieste, proposte in attesa, costo AI del mese | Un tocco apre la vista relativa; pulsante Vado a fare la spesa | Native (tile, button) |
| Lista della spesa | Lista todo dell'integrazione e, sotto, la sezione Suggeriti con il motivo | Aggiungere, spuntare (con registrazione facoltativa dell'acquisto), riordinare; un tocco aggiunge un suggerimento | Nativa (todo-list) più personalizzata per i suggeriti |
| Essenziali | Prodotti che devono esserci sempre, con presente, minima, obiettivo e stato | Aggiungere e togliere essenziali, modificare minima e obiettivo | Personalizzata |
| Zone | Una sezione per zona: ultimo controllo e affidabilità, ultimo ritaglio, prodotti, stato di camera\_show | Controlla ora, carica una foto, accendi la modalità mostra prodotto | Native più personalizzata di caricamento |
| Proposte | Variazioni riconosciute con ritaglio e quantità prima e dopo | Conferma, correggi, rifiuta, singolarmente o tutte | Personalizzata |
| Inventario | Prodotti per zona con quantità, scorta e stato | Più e meno, modifica, nuovo prodotto | Personalizzata |
| Scadenze | Calendario delle scadenze | Sola lettura | Nativa (calendar) |
| Storico | Ultimi movimenti con canale di origine | Annullare un movimento | Personalizzata |

Vado a fare la spesa: entità button e azione quartermaster.start\_shopping. Ricalcola lista e suggerimenti, invia alle entità notify scelte la lista e poi i suggeriti con il motivo (con pulsanti per aggiungerli), e attiva per un tempo configurabile la modalità spesa, in cui spuntare una voce registra l'acquisto. Si usa anche da automazioni, per esempio all'arrivo in un negozio.

Caricamento foto: card con scelta della zona e un campo file con accept="image/\*" e capture="environment": fotocamera dove l'app o il browser la offrono, altrimenti galleria. Nell'app Android di Home Assistant il campo file apre solo la galleria (issue home-assistant/android#6055); il README lo spiega e indica Telegram come via più rapida per scattare. La card ridimensiona l'immagine nel browser (lato lungo 1280 px, JPEG qualità 80, rotazione EXIF corretta, metadati rimossi) e la invia con un comando websocket dell'integrazione, che controlla il permesso di scrittura, valida tipo e dimensione (massimo 2 MB) e la passa alla pipeline di visione.

Card personalizzate: elementi Lit con customElements.define, registrazione in window.customCards, getStubConfig e getConfigForm per l'editor, getGridOptions per le sezioni. Leggono dalle entità e dai comandi websocket dell'integrazione; scrivono tramite le azioni dell'integrazione.

Distribuzione: repository HomeAssistant-Quartermaster-Dashboard di tipo dashboard nel catalogo del Marketplace, che lo aggiunge automaticamente come risorsa della dashboard. Se l'integrazione è installata e la risorsa manca, l'integrazione crea una segnalazione nelle riparazioni con il collegamento al repository; se non esiste un modo documentato per verificare la risorsa, l'avviso compare nel README e nel config flow.

### 6.6 Entità

| Entità | Tipo | Contenuto |
| --- | --- | --- |
| Lista della spesa | todo | Voci automatiche, da suggerimento e manuali |
| Da comprare | sensor | Numero di essenziali sotto scorta, con elenco negli attributi |
| Suggerimenti spesa | sensor | Numero, con elenco e motivi negli attributi |
| Prodotti in scadenza | sensor | Numero entro la soglia configurata, con elenco negli attributi |
| Ultimo controllo (una per zona) | sensor | Data dell'ultimo aggiornamento e affidabilità |
| Ultimo prelievo riconosciuto (una per zona con camera\_show) | sensor | Prodotto, quantità, affidabilità e ora |
| Foto richieste | sensor | Richieste di foto in attesa |
| Proposte in attesa | sensor | Numero di conferme pendenti |
| Analisi del giorno | sensor (diagnostica) | Chiamate ad AI Task del giorno rispetto al limite |
| Controlla zona (uno per zona) | button | Avvia il controllo |
| Vado a fare la spesa | button | Avvia la modalità spesa |
| Annulla ultimo movimento | button | Annulla l'ultimo movimento registrato |
| Modalità aggiornamento | select | Conferma o automatica |
| Modalità mostra prodotto (una per zona con camera\_show) | switch | Attiva camera\_show; si spegne da sola |
| Scadenze | calendar | Date di scadenza come eventi |
| Zona in errore (una per zona) | binary\_sensor | Telecamera non disponibile o analisi fallita ripetutamente |

### 6.7 Azioni dell'integrazione

ask, check\_zone, analyze\_image, adjust\_stock, undo\_last, set\_stock, add\_product, set\_essential, get\_suggestions, start\_shopping, refresh\_shopping\_list, arm\_show\_mode, confirm\_pending, import\_from\_todo, export\_data, import\_data (tutte nel dominio quartermaster). Le azioni di lettura e analisi restituiscono dati di risposta, così si usano in script e automazioni.

### 6.8 Lista della spesa, essenziali e suggerimenti

- La lista è un'entità todo dell'integrazione e si ricalcola dopo ogni variazione di inventario e una volta al giorno.
- Essenziali: prodotti con essential attivo, scorta minima obbligatoria, scorta obiettivo facoltativa. Si gestiscono dalla vista Essenziali, a voce o in chat ("aggiungi il caffè agli essenziali, almeno due pacchi") e con l'importazione da una lista todo.
- Da comprare: essenziale con quantità totale (somma di tutte le zone) sotto la scorta minima. Entra in lista da solo con quantità = target\_stock − totale; senza target\_stock, min\_stock − totale, almeno 1. Compare una sola volta.
- Suggerito, sempre con un motivo leggibile: essenziale sopra la minima ma sotto l'obiettivo ("ne hai 1, di solito ne tieni 4"); essenziale in una zona non aggiornata da più di N giorni, predefinito 7 ("il frigo non è aggiornato da 9 giorni"); prodotto non essenziale segnato come finito negli ultimi 30 giorni ("l'hai finito il 2 novembre").
- A posto: tutto il resto.
- I suggerimenti non entrano in lista da soli: compaiono nella notifica di Vado a fare la spesa, nella sezione Suggeriti, nel sensore Suggerimenti spesa e a voce ("cosa mi suggerisci di prendere?"). Un tocco, un pulsante della notifica o un "sì" li aggiunge con origin=suggestion.
- Durante la spesa "ho ancora caffè?" restituisce quantità, zona ed età del dato.
- Spuntare una voce registra l'acquisto con la quantità della voce: sempre in modalità spesa, a scelta fuori.
- Copia facoltativa unidirezionale verso un'entità todo esterna (Bring!, Todoist, KitchenOwl, Mealie, lista locale): voci tracciate per uid; quelle aggiunte a mano nella lista esterna non vengono toccate.

### 6.9 Notifiche

Nelle opzioni si scelgono una o più entità notify per ogni tipo di messaggio: richieste di foto, esito dei controlli, prodotti in scadenza, prelievi riconosciuti dalla telecamera, essenziali finiti, lista e suggerimenti della spesa, riepilogo settimanale. Silenzio notturno configurabile. Se un'entità non è disponibile si passa alla successiva.

## 7. Modelli e costi

L'integrazione non contiene client verso i provider. Nelle opzioni si scelgono:

- l'entità AI Task per le immagini (modello con visione);
- l'entità AI Task per i compiti testuali strutturati (anche un modello più economico);
- l'agente di conversazione per Telegram, dashboard e quartermaster.ask (deve supportare gli strumenti della LLM API).

Per la documentazione utente:

- Provider con integrazione ufficiale (OpenAI, Google, Anthropic, OpenRouter) o locali (Ollama): utilizzabili se supportano AI Task e, per la conversazione, gli strumenti.
- Provider senza integrazione ufficiale (per esempio Groq o Workers AI): tramite OpenRouter, oppure tramite Cloudflare AI Gateway con un'integrazione della community.
- Il README contiene la tabella dei provider testati, con l'esito sul set di valutazione del progetto e il supporto a più allegati.

Contenimento dei costi:

- Analisi solo quando arriva un'immagine utile; ritaglio e ridimensionamento a lato lungo 1280 px, JPEG qualità 80.
- Analisi saltata con hash percettivo per immagini invariate.
- Al modello si passano solo i prodotti attesi nella zona.
- Limite giornaliero di analisi configurabile, esposto dal sensore diagnostico.
- Le domande frequenti (lista, mancanti, suggerimenti) sono servite dagli strumenti senza chiamate al modello oltre a quella dell'agente.

## 8. Sicurezza e privacy

- Nessuna porta e nessun servizio aggiuntivo: tutto gira dentro Home Assistant.
- Telegram: solo le chat consentite nell'integrazione Telegram bot e, in più, nell'elenco delle opzioni di Quartermaster; messaggi di gruppo ignorati salvo opzione esplicita.
- Websocket e dashboard: lettura per gli utenti autenticati, scrittura per amministratori o utenti scelti.
- Alexa: nessun endpoint nell'integrazione; esposizione e collegamento dell'account sono quelli documentati da Home Assistant per la custom skill; permessi ridotti per il canale alexa (sezione 6.4).
- Prompt injection: testo in immagini, messaggi e frasi vocali trattato come dato; argomenti degli strumenti validati; quantità limitate a valori plausibili; variazioni oltre il 50% su più prodotti, creazione di prodotti ed essenziali sempre da confermare.
- Minimizzazione: al provider va solo il ritaglio della zona o l'area di presentazione di camera\_show; foto e fotogrammi temporanei cancellati dopo l'analisi; diagnostica con nomi utente, chat ID e percorsi oscurati.
- Audit: ogni scrittura nel journal con canale, utente e affidabilità; ogni movimento è annullabile.
- Supply chain: nessuna dipendenza Python esterna; dipendenze del frontend bloccate con lockfile; Dependabot; CodeQL, bandit e pip-audit in CI; SECURITY.md con segnalazione privata delle vulnerabilità.
- Limiti di frequenza: messaggi per chat, richieste vocali per minuto, analisi di immagini per ora.

## 9. Repository

### 9.1 HomeAssistant-Quartermaster (integrazione)

```
HomeAssistant-Quartermaster/
├── custom_components/quartermaster/
│   ├── __init__.py
│   ├── manifest.json
│   ├── config_flow.py
│   ├── const.py
│   ├── diagnostics.py
│   ├── llm.py
│   ├── notify.py
│   ├── services.py / services.yaml
│   ├── todo.py / sensor.py / binary_sensor.py / button.py
│   ├── select.py / switch.py / calendar.py
│   ├── core/ (inventory.py, zones.py, shopping.py, suggestions.py, confirmations.py, voice.py)
│   ├── storage/ (base.py, sqlite.py, migrations/)
│   ├── vision/ (acquire.py, quality.py, phash.py, analyze.py, show.py, schema.py)
│   ├── channels/ (telegram.py, websocket.py)
│   ├── brand/ (icon.png, logo.png)
│   └── translations/ (en.json completo, it.json)
├── tests/ (pytest-homeassistant, fixture di immagini e dialoghi)
├── docs/ (guida utente, provider testati, alexa.md)
├── examples/ (automazioni, alexa/ con modello di interazione e intent_script)
├── scripts/ (lint, dev-bootstrap)
├── .github/workflows/ (tests, security, hassfest, hacs, codeql, issues, tag-and-release)
├── .devcontainer/, docker-compose.yml
├── .ruff.toml, .mcp.json
├── hacs.json
├── README.md, CHANGELOG.md, CONTRIBUTING.md, SECURITY.md, LICENSE
├── CLAUDE.md
└── TECHNICAL_NOTES.md
```

Requisiti del Marketplace: una sola integrazione sotto custom\_components; manifest.json con domain, name, version, documentation, issue\_tracker, codeowners, integration\_type (service) e categories (da una a tre); icona e logo in brand/ dentro la cartella dell'integrazione; translations/en.json completo e nessun strings.json; hacs.json nella radice; README con immagini; descrizione, topic e issue attive sul repository; release GitHub pubblicate con tag uguale a version del manifest.

hacs.json: name, homeassistant (versione minima), zip\_release: true, filename: quartermaster.zip; country omesso.

### 9.2 HomeAssistant-Quartermaster-Dashboard (strategia e card)

```
HomeAssistant-Quartermaster-Dashboard/
├── src/ (TypeScript + Lit: strategia, card Suggeriti, Essenziali, Proposte, Inventario, Storico, caricamento foto)
├── dist/quartermaster-dashboard.js (file compilato, incluso nella release)
├── tests/
├── .github/workflows/ (lint, test, build, hacs con category plugin, release)
├── package.json, lockfile
├── hacs.json (name, filename, homeassistant)
├── README.md (con immagini)
├── CHANGELOG.md, LICENSE, SECURITY.md
└── CLAUDE.md
```

Regole: il Marketplace cerca il file prima tra gli asset dell'ultima release, poi nella radice e in dist; poiché il nome quartermaster-dashboard.js non deriva dal nome del repository, hacs.json contiene filename: quartermaster-dashboard.js; il file viene registrato come risorsa di tipo module in /local/community/HomeAssistant-Quartermaster-Dashboard/; versioni dei due repository rilasciate in coppia, con tabella di compatibilità in entrambi i README; la dashboard dichiara la versione minima dell'integrazione e mostra un messaggio chiaro se l'integrazione manca o è troppo vecchia; versione minima di Home Assistant almeno 2026.5, necessaria per le strategie nella finestra Nuova dashboard.

### 9.3 Convenzioni comuni

Le stesse del repository HomeAssistant-OctopusEnergyIT dell'autore:

- Ruff come unico formatter e linter (.ruff.toml, scripts/lint); mypy.
- Dati per voce di configurazione in entry.runtime\_data.
- Testi per l'utente, eccezioni comprese, solo tramite chiavi di traduzione in translations/en.json e it.json.
- Codice e commenti in inglese.
- Cambiare un unique\_id è una modifica incompatibile da documentare nel CHANGELOG.
- Nessun flag di debug attivo nei commit; nessun segreto né dato personale nel repository, negli esempi o nelle fixture.
- Ambiente di sviluppo su macOS con Docker Desktop (immagini compatibili con Apple Silicon): devcontainer e docker-compose.yml con un Home Assistant locale sulla porta 8123 e ha-mcp; scripts/dev-bootstrap crea utente e token di sviluppo in config/.dev/ (esclusa da git).
- CLAUDE.md breve (comandi, struttura, regole, scelte da non rimettere in discussione, rilascio); TECHNICAL\_NOTES.md per l'architettura approfondita e per le decisioni prese durante lo sviluppo; .mcp.json con context7.
- Test con pytest-homeassistant, il pacchetto ufficiale pubblicato da home-assistant/core (versione allineata a Home Assistant, Python 3.14.2 o successivo, helper da pytest\_homeassistant.common); non usare pytest-homeassistant-custom-component.
- Le pull request a home-assistant/brands e hacs/default partono dai fork dell'autore.

## 10. Configurazione

Config flow in più passi, tutti con selettori di Home Assistant:

1. Nome della casa e lingua.
2. Modelli: entità AI Task per le immagini e per il testo; agente di conversazione.
3. Zone iniziali (proposte: Dispensa, Frigorifero, Freezer), ognuna con entità camera e ritaglio facoltativi.
4. Canali: istanza Telegram bot e chat consentite; voce (durata della sessione di quartermaster.ask, permessi del canale alexa).
5. Notifiche: entità notify per tipo di messaggio, silenzio notturno.
6. Essenziali iniziali e importazione facoltativa da un'entità todo esistente.

L'options flow permette di modificare tutto; zone, prodotti, essenziali e camera\_show si gestiscono anche dalla dashboard. La versione minima di Home Assistant è la prima che supporta tutte le funzioni usate (AI Task con structure e allegati, strumenti da llm.py, Telegram bot configurabile da interfaccia, strategie nella finestra Nuova dashboard) e va scritta in manifest e hacs.json.

## 11. CI, rilascio e pubblicazione

Workflow dell'integrazione:

- tests: ruff, mypy, pytest con copertura minima del 90% per core e storage, contro la versione stabile e la beta di Home Assistant.
- security: bandit e pip-audit.
- hassfest e hacs (category integration, senza ignore) a ogni push e pull request.
- codeql: analisi di Python.
- issues: gestione delle issue.
- tag-and-release: l'aumento di version nel manifest su main esegue test e controlli di sicurezza, poi crea tag, ZIP e release GitHub dalla sezione corrispondente del CHANGELOG. La versione si aumenta solo su richiesta esplicita dell'autore.

Workflow della dashboard: lint, test, build (con verifica che dist corrisponda al sorgente), hacs con category plugin, release.

Pubblicazione: da Home Assistant 2026.11 HACS è integrato come Marketplace; il catalogo resta hacs/default e hacs.json non cambia. Il README copre l'installazione dal Marketplace e, per le versioni precedenti, da HACS. Percorso: release 1.0.0, uso come repository personalizzato, poi pull request a hacs/default con tutti i controlli verdi.

Quality scale: livello Bronze per la 1.0 (action-setup, appropriate-polling, brands, common-modules, config-flow-test-coverage, config-flow, dependency-transparency, docs-actions, docs-triggers, docs-conditions, docs-high-level-description, docs-installation-instructions, docs-removal-instructions, entity-event-setup, entity-unique-id, has-entity-name, runtime-data, test-before-configure, test-before-setup, unique-config-entry), Silver nelle versioni successive.

## 12. Piano di implementazione

Ogni fase si chiude con criteri di accettazione; non si passa alla successiva finché non sono soddisfatti. Si sviluppa sull'istanza di sviluppo; l'istanza reale dell'autore si usa solo per le verifiche pratiche e il collaudo, con il suo consenso.

### Fase 0 — Verifiche e scheletro

1. Rileggere sulle fonti ufficiali correnti: AI Task (structure, attachments), llm.py, conversation.process, Telegram bot (eventi, download dei file), intent\_script e skill Alexa, strategie e card personalizzate, requisiti del Marketplace.
2. Verificare che il dominio quartermaster sia libero nel catalogo del Marketplace e in home-assistant/brands.
3. Fissare la versione minima di Home Assistant.
4. Creare la struttura dei due repository secondo la sezione 9, con devcontainer e CI della sezione 11 dal primo commit.
5. Eseguire le verifiche pratiche elencate sotto, sull'istanza di sviluppo e con l'autore.

Verifiche pratiche:

- Telegram: inviare una foto al bot e verificare quale evento arriva e come l'integrazione la riceve e la scarica. Cloudflare AI Gateway: revisione del codice dell'integrazione della community alla versione fissata; con i modelli Workers AI scelti verificare che l'agente usi gli strumenti di Quartermaster tramite l'API Assist, che AI Task con structure restituisca JSON valido, che le immagini allegate arrivino al modello di visione, la qualità delle risposte in italiano e il consumo giornaliero di neuroni rispetto alla quota gratuita.
- Alexa: con l'intent\_script di esempio e uno script al posto di quartermaster.ask, verificare che il testo dello slot AMAZON.SearchQuery arrivi intero, che un reprompt vuoto chiuda la sessione e uno pieno tenga aperto il microfono, e che la risposta arrivi entro 8 secondi; verificare che la console accetti il nome di invocazione "dispensa" in sviluppo (altrimenti "dispensa di casa") e che "Alexa, dispensa" apra la skill.
- Telecamera: con la telecamera di prova e l'entità evento di movimento della stessa telecamera, misurare quanto è recente il fotogramma letto 1, 3 e 5 secondi dopo il movimento e l'impatto sulla batteria; verificare se camera.record funziona con questa telecamera (modalità clip) o se serve la modalità burst; mostrare 10 prodotti diversi e contare quanti ne riconosce un modello con visione. Con esito negativo camera\_show si implementa comunque ma non si attiva sull'istanza dell'autore.
- iOS: pagina di prova con un campo file accept="image/\*" capture="environment" nell'app di Home Assistant su un iPhone, per verificare se compare la fotocamera.
- Marketplace: verificare che il repository dashboard installato compaia come risorsa e la strategia nella finestra Nuova dashboard; cercare un modo documentato per verificare la risorsa dall'integrazione.

Criteri: CI verde su uno scheletro che si installa e si configura; esiti delle verifiche annotati in TECHNICAL\_NOTES.md e comunicati all'autore.

### Fase 1 — Archivio e inventario

1. StorageBackend e implementazione SQLite con migrazioni.
2. Servizio di inventario: prodotti, essenziali, zone, stock, movimenti, alias, annullamento.
3. Azioni adjust\_stock, undo\_last, set\_stock, add\_product, set\_essential, export\_data, import\_data.

Criteri: test unitari su tutte le operazioni; esportazione e reimportazione producono lo stesso stato; ogni movimento si annulla correttamente.

### Fase 2 — Entità, lista e suggerimenti

1. Entità todo con voci automatiche, da suggerimento e manuali.
2. Calcolo di da comprare, suggeriti e a posto con motivo leggibile; azioni get\_suggestions, refresh\_shopping\_list, start\_shopping con modalità spesa.
3. Sensori, pulsanti, select, switch, calendario, binary sensor.
4. Copia facoltativa verso un'entità todo esterna; importazione da todo.

Criteri: un essenziale sotto scorta compare una sola volta in lista; un essenziale tra minima e obiettivo compare solo tra i suggeriti; una zona non aggiornata genera il suggerimento con il motivo giusto; un suggerimento accettato entra in lista; le voci manuali restano intatte; l'importazione da una lista todo di prova funziona con anteprima.

### Fase 3 — Strumenti LLM e voce

1. llm.py con gli strumenti della sezione 6.1 e il frammento di prompt.
2. Regole dei consumi della sezione 6.2 con candidati e chiarimenti in sospeso.
3. Azione quartermaster.ask con sessioni per canale e presa in carico oltre 5 secondi.
4. Test con un agente simulato, poi con un provider reale.

Criteri: dieci frasi di prova in italiano e inglese producono i movimenti attesi; "ho finito il formaggio in frigo" con due formaggi produce una domanda e nessuna scrittura, e "la mozzarella" nella stessa sessione completa il consumo; "annulla" ripristina la quantità; un prodotto sconosciuto porta alla domanda sulla lista; le operazioni da confermare non scrivono senza conferma.

### Fase 4 — Visione e foto

1. Controlli di qualità, ritaglio, ridimensionamento, rimozione dei metadati, hash percettivo con Pillow.
2. Chiamata ad ai\_task.generate\_data con structure e allegati; ripiego con JSON testuale.
3. Confronto con l'inventario, proposte, conferme, apprendimento degli alias; azione analyze\_image.

Criteri: su almeno 30 foto reali etichettate (frigo, dispensa, scaffale) almeno l'85% dei prodotti riconosciuti con almeno un provider cloud; nessuna scrittura senza conferma in modalità conferma.

### Fase 5 — Zone e telecamere

1. Gestore delle zone con l'algoritmo della sezione 5, scheduler, limiti giornalieri, promemoria e richieste di foto.
2. Fonte camera\_show della sezione 5.1 con interruttore, notifica con Annulla e sospensione con segnalazione nelle riparazioni.

Criteri: telecamera non disponibile, fotogramma scuro o affidabilità sotto soglia portano alla richiesta di foto; limiti giornalieri e cooldown rispettati; con fotogrammi di prova, nessun prodotto mostrato non genera scritture e un prodotto riconosciuto sopra soglia genera un consumo annullabile; nessun fotogramma resta su disco dopo l'analisi.

### Fase 6 — Telegram

1. Eventi del Telegram bot per le chat consentite: testo all'agente, foto alla visione, pulsanti per conferme, scelta della zona e annullamento.
2. Notifiche, richieste di foto e lista della spesa su Telegram.

Criteri: una chat non consentita viene ignorata; una foto con didascalia "frigo" produce la proposta con pulsanti funzionanti.

### Fase 7 — Dashboard

1. Comandi websocket con controllo dei permessi.
2. Strategia con le viste della sezione 6.5.
3. Card personalizzate: Suggeriti, Essenziali, Proposte, Inventario, Storico, caricamento foto.
4. Prova su iOS, Android e tablet, in tema chiaro e scuro.

Criteri: la dashboard si crea dalla finestra Nuova dashboard; tutte le viste funzionano su telefono e tablet; una foto caricata produce una proposta; un utente senza permesso di scrittura non modifica dati.

### Fase 8 — Notifiche e Alexa

1. Instradamento delle notifiche con silenzio notturno e ripiego.
2. docs/alexa.md ed examples/alexa/ con modello di interazione, intent\_script e routine consigliate; prova sull'istanza dell'autore.

Criteri: "Alexa, chiedi a dispensa: ho finito il latte" registra il consumo, mette il latte in lista se essenziale e chiude la sessione; "ho finito il formaggio in frigo" con due candidati fa la domanda a microfono aperto e la risposta completa l'operazione; "cosa devo comprare?" e "cosa mi suggerisci di prendere?" ricevono risposte corrette; "controlla lo scaffale" produce una presa in carico e poi l'annuncio dell'esito; con l'opzione di sola lettura Alexa non scrive; la guida permette di ripetere la configurazione da zero.

### Fase 9 — Documentazione e rilascio

1. README con installazione, configurazione, provider testati, esempi e limitazioni note; guida utente in docs/; traduzioni.
2. Diagnostica, SECURITY.md, CONTRIBUTING.md.
3. Release 1.0.0 di entrambi i repository, poi richiesta di inclusione nel catalogo.

Criteri: un utente nuovo installa e configura tutto seguendo solo il README; validazioni del Marketplace e hassfest verdi.

## 13. Piano di test

- Unitari: archivio e migrazioni, calcolo di da comprare e suggeriti, logica delle zone, validazione dell'output dei modelli, strumenti LLM, sessioni di quartermaster.ask.
- Integrazione con pytest-homeassistant: config flow, entità, azioni, eventi Telegram simulati, AI Task e agente simulati.
- Dialoghi: frasi in italiano e inglese con risposte attese, compresi casi ambigui, prodotti sconosciuti e annullamento.
- Visione: cartella pubblica di foto etichettate senza persone né dati personali, con script che misura precisione e richiamo per provider e versione del prompt.
- camera\_show: fotogrammi etichettati con e senza prodotto mostrato, compresi fotogrammi con persone senza prodotto, per misurare i falsi positivi.
- Frontend: test dei componenti e prova manuale su iOS e Android.
- Sicurezza: chat non autorizzate, immagini e frasi con testo che tenta istruzioni, argomenti fuori intervallo, scritture vocali con l'opzione di sola lettura.

## 14. Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| Riconoscimento impreciso da foto | Conferma, soglie, prodotti attesi nel prompt, correzioni e alias |
| Falsi positivi di camera\_show | Nessuna scrittura senza conferma dell'utente, soglia alta per i messaggi, when\_armed, Annulla sull'esito, cooldown, solo prodotti attesi nella zona |
| Fotogrammi vecchi o lenti con telecamere cloud | Verifica in Fase 0, più fotogrammi per innesco, sospensione automatica |
| Telecamere a batteria | Limiti giornalieri per zona e per fonte |
| Provider con qualità diversa | Tabella dei provider testati, ripiego per l'output strutturato |
| Nome di invocazione obbligatorio con Alexa | Nome breve, routine per le frasi fisse; Assist e Telegram senza vincolo |
| Comandi vocali da chiunque sia vicino a un Echo | Permessi ridotti, conferma a voce, annullamento, journal, opzione di sola lettura |
| API di Home Assistant che cambiano | Versione minima dichiarata, test contro stabile e beta |
| Manutenzione a lungo termine | Codice modulare, CI completa, contributi aperti |

## 15. Evoluzioni dopo la 1.0

- Adattatori di archivio verso Grocy ed EverShelf.
- Nuove fonti: bilance ESPHome, camere interne con sensore porta.
- Registrazione degli acquisti da scontrini e email d'ordine.
- Previsione dei consumi dallo storico come ulteriore motivo di suggerimento.

## 16. Istruzioni per Claude Code

- Lavora una fase alla volta e fermati ai criteri di accettazione mostrando i risultati.
- Basa ogni scelta sulla documentazione ufficiale corrente (sezione 17); non andare a memoria. Se la documentazione non copre un punto, fermati e chiedi all'autore, poi annota la decisione in TECHNICAL\_NOTES.md.
- Non usare API interne non documentate di Home Assistant.
- Non introdurre dipendenze Python esterne senza chiedere; preferisci la libreria standard e ciò che Home Assistant già include.
- Codice tipizzato e asincrono, con test; lavoro bloccante (SQLite, Pillow) sempre nell'executor.
- Sviluppa sull'istanza di sviluppo; sull'istanza reale dell'autore solo letture, salvo consenso esplicito.
- Nessun segreto nel repository; nessun dato personale negli esempi o nelle fixture.
- Aggiorna il CHANGELOG e annota in TECHNICAL\_NOTES.md ogni scostamento da questa specifica.

## 17. Base documentale

### 17.1 Punti confermati dalla documentazione ufficiale

| Tema | Cosa stabilisce la documentazione | Fonte |
| --- | --- | --- |
| Manifest | version obbligatorio per le custom; domain uguale al nome della cartella; integration\_type esplicito; requirements solo se non già inclusi | developers.home-assistant.io/docs/creating\_integration\_manifest |
| Marketplace | Da 2026.11 HACS è integrato come Marketplace; catalogo hacs/default e hacs.json invariati; inclusione con pull request a hacs/default | contributors.home-assistant.io/marketplace |
| Struttura | Una integrazione per repository; icona e logo in brand/ nella cartella dell'integrazione; translations/en.json completo; nessun strings.json per le custom | contributors.home-assistant.io/marketplace/guides/integrations |
| Controlli del catalogo | Azione HACS e hassfest; controlli archived, brands, description, hacsjson, images, information, integration\_categories, issues, topics | contributors.home-assistant.io/marketplace/reference/validation |
| Release | Versione = tag di una release pubblicata, uguale a version del manifest; zip\_release con filename; homeassistant in hacs.json per la versione minima | contributors.home-assistant.io/marketplace/guides/releases e reference/hacs-json |
| Quality scale | Bronze obbligatorio per le nuove integrazioni core; riferimento consigliato per le custom | developers.home-assistant.io/docs/core/integration-quality-scale |
| Strumenti LLM | llm.py con async\_get\_tools(hass, llm\_context, api\_id) che restituisce llm.LLMTools o None; ogni Tool imposta integration (obbligatorio per le custom da 2027.10), può dichiarare annotations e restituisce ToolResult (i dict non funzionano da 2027.11) | developers.home-assistant.io/docs/core/llm |
| Validazione | Da 2026.9 Home Assistant usa probatio; voluptuous resta disponibile come alias; il codice nuovo importa probatio | developers.home-assistant.io/blog/2026/09/30/probatio-validation-engine |
| AI Task | ai\_task.generate\_data con task\_name, instructions, entity\_id, structure e attachments (elenco di file nel formato del selettore media); risposta con data e conversation\_id; esempio con media-source://camera/\<entity\_id> | home-assistant.io/actions/ai\_task.generate\_data |
| Conversazione | conversation.process con text, agent\_id, language, conversation\_id; la risposta contiene conversation\_id e continue\_conversation, vero quando l'agente aspetta una risposta | home-assistant.io/actions/conversation.process; contributors.home-assistant.io/core/internals/conversation-api |
| Telegram | Eventi telegram\_text, telegram\_command, telegram\_callback, telegram\_attachment sull'entità evento del bot; telegram\_bot.download\_file restituisce file\_path; chat consentite come sottovoci; inline\_keyboard | home-assistant.io/integrations/telegram\_bot |
| Lista todo | TodoListEntity con flag CREATE, DELETE, UPDATE, MOVE, SET\_DESCRIPTION\_ON\_ITEM e altri | developers.home-assistant.io/docs/core/entity/todo |
| Skill Alexa | Custom skill con Lambda di inoltro e collegamento dell'account su /auth/authorize e /auth/token; intent\_script con speech e reprompt; gli slot arrivano come variabili; il risultato dell'azione è action\_response; le routine accorciano l'invocazione | home-assistant.io/integrations/alexa.intent e intent\_script |
| Sessione Alexa | Con la sessione aperta Alexa apre il microfono per qualche secondo; con un reprompt lo riapre una volta, poi SessionEndedRequest; la skill ha 8 secondi per rispondere | developer.amazon.com, custom skills: manage-skill-session-and-session-attributes, progressive-response-api-reference |
| Invocazione senza nome | Disponibile solo per skill nascoste di Alexa Smart Properties già pubblicate, nelle lingue inglesi | developer.amazon.com, understand-name-free-interaction-for-custom-skills |
| Ring | Entità camera live\_view e last\_recording (questa richiede Ring Protect); movimento e campanello come entità evento; comunicazione solo via cloud | home-assistant.io/integrations/ring |
| Test | pytest-homeassistant ufficiale, versione allineata a Home Assistant, Python 3.14.2 o successivo | pypi.org/project/pytest-homeassistant |
| Invocazione in italiano | Forme ammesse: domanda a, chiedi a, di' a, vai su, usa, e avvia, apri, lancia, accedi a, parla con seguite da e; avvio con il solo nome ("Alexa, \<nome>") o con apri, avvia, comincia, inizia, lancia, accedi a, gioca con, usa | developer.amazon.com, understanding-how-users-invoke-custom-skills; choose-the-invocation-name-for-a-custom-skill |
| Registrazione da telecamera | camera.record salva un mp4 di durata indicativa; richiede l'integrazione stream e un percorso scrivibile; lookback solo con uno stream HLS attivo | home-assistant.io/actions/camera.record |
| Cloudflare AI Gateway | Endpoint compatibile OpenAI con modelli indicati come provider/modello (Workers AI, Groq, Google AI Studio e altri); log, cache, limiti di frequenza e di spesa; funzioni base gratuite; chiavi dei provider salvate sul gateway oppure credito Cloudflare con il 5% sull'acquisto; per le singole chiamate Cloudflare indica come preferita la nuova REST API, l'endpoint /compat resta funzionante | developers.cloudflare.com/ai-gateway: usage/chat-completion, reference/pricing, reference/limits, features/spend-limits |
| Workers AI | 10.000 neuroni gratuiti al giorno su piano Free e Paid; gemma-4-26b-a4b-it con visione, chiamata di strumenti e nessuna conservazione dei dati, disponibile nel piano Free (9.091 neuroni per milione di token in ingresso, 27.273 in uscita); glm-4.7-flash con chiamata di strumenti, disponibile nel piano Free | developers.cloudflare.com/workers-ai: platform/pricing, models; changelog 2026-07-28 |

### 17.2 Punti non coperti dalla documentazione e regola adottata

| Punto | Regola |
| --- | --- |
| Pannello nella barra laterale registrato da Python; caricamento di JavaScript da un'integrazione (add\_extra\_js\_url) | Non si usano; il frontend arriva dal repository dashboard |
| Fotocamera dal campo file di una card nell'app Android | Non disponibile (issue home-assistant/android#6055): fotocamera dove c'è, altrimenti galleria; iOS da verificare in Fase 0 |
| Limiti di dimensione dei messaggi websocket | Ridimensionamento nel browser e limite di 2 MB |
| Verifica della risorsa della dashboard dall'integrazione | Da cercare in Fase 0; in mancanza, avviso nel README e nel config flow |
| Chiamata ad AI Task dal codice di un'altra integrazione | Si chiama l'azione documentata ai\_task.generate\_data come servizio con risposta (hass.services.async\_call con blocking e return\_response) |
| Foto inviate al bot Telegram e ascolto dell'entità evento da un'integrazione | Da verificare in Fase 0 prima di implementare il canale |
| Reprompt con testo vuoto nell'intent\_script | Il codice attuale non lo imposta e la sessione si chiude; da verificare in Fase 0, ripiego nella sezione 6.4 |
| Durata della memoria di un conversation\_id | Dipende dall'agente: quartermaster.ask conserva da sé conversation\_id e chiarimento in sospeso, con scadenza |
| Attributi di sessione Alexa | Non documentati per l'integrazione: non si usano |
| Freschezza del fotogramma Ring dopo un movimento | Da misurare in Fase 0 |
| Supporto a più allegati per provider | Dichiarato nella tabella dei provider testati; ripiego sul fotogramma migliore |
| render\_readme in hacs.json | Non citato nella guida del Marketplace: non si usa; le informazioni stanno nel README |
| Modelli e prezzi dei provider | Si verificano sulle pagine ufficiali dei provider al momento della scelta |
| Integrazione di Cloudflare AI Gateway in Home Assistant | Non esiste un'integrazione ufficiale e quella OpenAI non accetta un URL personalizzato. Nell'ambiente dell'autore si usa l'integrazione della community cloudflare\_ai\_gateway, con versione fissata e revisione del codice; Quartermaster non dipende da essa e resta indipendente dal provider |
| Output strutturato con i modelli Workers AI scelti | Da verificare in Fase 0; in caso di problemi si usa la modalità di ripiego con JSON nel testo (sezione 4.2) |

### 17.3 Fonti da consultare durante lo sviluppo

Home Assistant (developers, contributors, home-assistant.io), documentazione del Marketplace, Alexa Skills Kit, Telegram Bot API, Lit, TypeScript, Ruff, bandit, pip-audit, GitHub Actions (hacs/action, home-assistant/actions/hassfest). context7 aiuta per le librerie, ma in caso di dubbio prevale il sito ufficiale.

## Riferimenti

- Marketplace, integrazioni custom: https://contributors.home-assistant.io/marketplace/guides/integrations
- AI Task: https://www.home-assistant.io/integrations/ai\_task/
- Azione ai\_task.generate\_data: https://www.home-assistant.io/actions/ai\_task.generate\_data/
- LLM API per sviluppatori: https://developers.home-assistant.io/docs/core/llm/
- Azione conversation.process: https://www.home-assistant.io/actions/conversation.process/
- API di conversazione: https://contributors.home-assistant.io/core/internals/conversation-api/
- Telegram bot: https://www.home-assistant.io/integrations/telegram\_bot/
- Skill Alexa in Home Assistant: https://www.home-assistant.io/integrations/alexa.intent/
- intent\_script: https://www.home-assistant.io/integrations/intent\_script/
- Alexa, sessione e reprompt: https://developer.amazon.com/en-US/docs/alexa/custom-skills/manage-skill-session-and-session-attributes.html
- Alexa, tempo di risposta: https://developer.amazon.com/en-US/docs/alexa/custom-skills/progressive-response-api-reference.html
- Alexa, interazione senza nome: https://developer.amazon.com/docs/custom-skills/understand-name-free-interaction-for-custom-skills.html
- Alexa Devices: https://www.home-assistant.io/integrations/alexa\_devices
- Ring: https://www.home-assistant.io/integrations/ring/
- OpenRouter: https://www.home-assistant.io/integrations/open\_router/
- Quality scale: https://developers.home-assistant.io/docs/core/integration-quality-scale/
