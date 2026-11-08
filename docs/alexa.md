# Alexa

Quartermaster has no Alexa code of its own. A private Alexa custom skill, set up as described in Home Assistant's [Alexa Intents](https://www.home-assistant.io/integrations/alexa.intent/) documentation, sends what you say to the `quartermaster.ask` action through `intent_script`. Quartermaster passes it to your conversation agent with its tools and returns what Alexa should say.

```text
You:    Alexa, ask pantry if I still have milk
Alexa:  You have 2 bottles of milk in the fridge.

You:    Alexa, chiedi a dispensa: ho finito il formaggio in frigo
Alexa:  In frigo ho parmigiano e mozzarella. Quale hai finito?
You:    La mozzarella
Alexa:  Fatto, mozzarella finita. È tra gli essenziali: l'ho messa in lista.
```

## What you need

- Home Assistant reachable over HTTPS from the internet (Home Assistant Cloud, a reverse proxy or a tunnel).
- An Amazon developer account and an AWS account for the forwarding Lambda.
- A conversation agent with an LLM chosen in the Quartermaster options (**Conversation agent**). The Assist API must be attached to it so it can use the Quartermaster tools; since Home Assistant 2026.11 only an administrator can attach it.

## Set up the skill

1. Follow the [Alexa Intents](https://www.home-assistant.io/integrations/alexa.intent/) guide to create a **custom skill**, the forwarding Lambda and account linking. Use one skill with every language you speak at home.
2. Add each locale to the skill (for example it-IT, en-US, en-GB). In each one, open the JSON editor and paste the matching file from [`examples/alexa`](../examples/alexa):
   - `interaction_model.it-IT.json` (invocation name "dispensa", intent `QuartermasterQueryIT`);
   - `interaction_model.en-US.json` and `interaction_model.en-GB.json` (invocation name "pantry", intent `QuartermasterQueryEN`).

   Then build the model.
3. If the console refuses a one-word invocation name, use two words ("dispensa di casa", "home pantry") and say that instead.
4. Copy [`examples/alexa/quartermaster_alexa.yaml`](../examples/alexa/quartermaster_alexa.yaml) into your packages folder, or merge it into `configuration.yaml`. Replace `REPLACE_WITH_SKILL_ID` with your skill ID (`amzn1.ask.skill.…`), then restart Home Assistant.
5. In the Alexa app, enable the skill under **Your Skills → Dev** and link your Home Assistant account.

## How to talk to it

| Italian | English |
| --- | --- |
| "Alexa, chiedi a dispensa se ho ancora il latte" | "Alexa, ask pantry if I still have milk" |
| "Alexa, di' a dispensa di segnare il latte finito" | "Alexa, tell pantry to mark the milk finished" |
| "Alexa, apri dispensa e segna il latte finito" | "Alexa, open pantry and add milk" |
| "Alexa, dispensa" → "Dimmi" → "ho finito il latte" | "Alexa, pantry" → … |

Why every utterance has a carrier word (for example "se", "di", "if", "to"): Alexa does not accept a free-text slot on its own as a sample, so each sample starts with a common word. The words after it reach Quartermaster intact.

Home Assistant does not tell `intent_script` which language the request was in. For this reason each language has its own intent, and the example passes `language` to `quartermaster.ask`. The reply to "Alexa, dispensa" alone (launch) cannot know the language: change the two launch texts in the YAML if your home mainly speaks another language.

## Questions and the microphone

When the agent asks something ("Quale hai finito?"), `quartermaster.ask` returns `continue_conversation: true` and a short reprompt, so Alexa keeps the microphone open for the answer. The answer arrives as a new request, and Quartermaster keeps the thread for the session time in the options (default 120 seconds).

When the reply asks nothing, the reprompt is empty and the session ends.

Alexa gives a skill 8 seconds to answer. If the agent is slower than 5 seconds, Quartermaster replies "Ci sto lavorando, ti avviso quando ho finito" and sends the answer later to the notify entities chosen under **Late voice replies**. For example, pick an Echo's announce entity from the Alexa Devices integration.

## Phrases without the skill name

Interaction without the invocation name is not available for this kind of skill in Italian. To get it, create an **Alexa routine** with a fixed phrase ("Alexa, cosa manca in casa") whose action is to say "chiedi a dispensa cosa manca".

## Permissions

Anyone near an Echo can talk to the skill. In the Quartermaster options:
- **Alexa can only read** blocks every change from Alexa (stock, list, essentials).
- Without it, Alexa can read, record what you used or bought, and change the list.
- New products, essentials and large purchases always need an explicit "yes" in the same session.

Every change is in the journal with channel `alexa` and can be undone ("annulla", the Undo button, or the History view of the dashboard).

## Troubleshooting

- **"Mi dispiace, non ho capito" from Home Assistant:** the conversation agent in the options is empty or is Home Assistant's built-in agent. Choose an LLM agent with the Assist API attached.
- **Alexa says nothing or "there was a problem":** check the skill ID in the YAML and the Lambda logs. Then enable debug logging for `homeassistant.components.alexa` and `custom_components.quartermaster`.
- **Words missing from what Quartermaster received:** enable debug logging and look at the `text` passed to `quartermaster.ask`. If the carrier word took part of the sentence, add a sample in the interaction model.
