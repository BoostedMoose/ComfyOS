# Network plugin

Shows in ComfyOS whether a device in the home network is reachable – typically as presence: "phone in the Wi-Fi" means "somebody is at home".

## What it can do

- Watch freely named devices in the home network (address + port), at most 30.
- Every device appears in ComfyOS as a **presence** with the state *present* / *away*.
- Flows react to it through "Gerätestatus → Anwesenheit" (e.g. "phone comes home → light in the hallway on").

## How it checks

ComfyOS regularly tries to open a TCP connection to the device. Every answer counts:

| Answer of the device | Meaning |
|---|---|
| connection accepted | present |
| connection refused (port closed) | present – the device answered |
| no answer | nothing yet; after the waiting time: away |

Phones sleep and then do not answer every attempt. Therefore a device only counts as away after the configured time without an answer (default 10 minutes); ComfyOS reports "present" with the first answer. After a restart the state is *unknown* at first – so a restart never looks as if somebody had left.

## Setting up

1. Give the device a fixed address in the router.
2. Enter name, address and port under *System → Plugins → Netzwerk*.
3. **Jetzt prüfen** shows whether the device answers.

Which port? Usually `62078` for an iPhone. Otherwise try `80` or `443` – it is enough if the device actively refuses the attempt.

## Limits

- No ICMP ping: it needs special rights on most systems.
- Some phones do not answer at all for a long time while asleep. Then increase the waiting time.
- Only addresses in the home network are checked; ComfyOS blocks everything else.

## Settings

| Field | Default | Meaning |
|---|---|---|
| Prüfen alle … Sekunden | 30 | distance between two attempts (10–600) |
| Abwesend nach … Minuten ohne Antwort | 10 | waiting time until "away" (1–120) |

## Tests

```
python public/plugins/network/tests/test_network_plugin.py
```

The tests run without ComfyOS and open no real connection.
