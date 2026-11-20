# Sonos plugin

Connects Sonos speakers locally in the home network – without a Sonos account, without the cloud and without further software.

## What it can do

- One device of the kind **speaker** per room: playing/paused, volume, mute, current track with its cover picture.
- Start and pause playback, track forward and back, volume, mute, start a Sonos favourite.
- **Sound of this computer** (since plugin 1.2.0): the button with the cast symbol on the speaker tile plays the sound
  of the computer the page is open on (see "Sound of this computer" below).
- Join rooms into a group and take them out again (in the tile).
- Flows react through "Gerätestatus → Wiedergabe" and control with the node "Wiedergabe".

Stereo pairs, surround speakers and subwoofers belong to the room of their main speaker. Each of these further boxes
is a device of its own without control ("Wohnzimmer (hinten links)", "Küche (rechts)"): it can be placed on the floor
plan and stands in its room, and it shows what its room plays. Playback and volume are controlled through the room.

## Setting up

1. Check in the Sonos app: *Account → Privacy & Security → UPnP* is switched on (default).
2. Tap **Lautsprecher suchen** under *System → Plugins → Sonos* – or enter the address of one speaker.
   One speaker is enough, it knows the whole household.
3. Adopt rooms under "Neu bei Sonos". If a hub (e.g. the Home Base) already knows the speakers, they appear
   without adopting: name and room come from the hub, control is direct.

## How it talks to the speakers

Every speaker offers UPnP services on port 1400 (HTTP with XML, without login). That is not an official
interface but has been the way for local control for years. ComfyOS only queries and sends commands:

| What | How often |
|---|---|
| search in the home network (SSDP, UDP) | only at the push of a button |
| playback, track, volume | every few seconds with an open page, otherwise every 15 seconds |
| rooms, groups, favourites | about once per minute |

There is no event subscription: the speakers never call ComfyOS back, and ComfyOS opens no port for that.

## Sound of this computer

1. Open ComfyOS in Chrome or Edge on the computer, over HTTPS (*System → Erreichbarkeit*). Phones and tablets cannot
   share sound; the button is missing there. For Firefox see below.
2. Tap the cast symbol on the tile of the room. The browser asks what to share: choose **the whole screen** and tick
   **"Systemaudio teilen"** for everything the computer plays, or one tab for the sound of that tab only. The picture
   is not used.
3. The room plays the sound, a few seconds behind the computer – fine for music, not for films or games. Further rooms
   join with their own button; the same button or "Freigabe beenden" in the browser ends it. Closing the page ends it too.

**Firefox** never passes sound along when sharing the screen. There the button asks for a recording device instead:
pick **"Stereomix"** in Firefox's dialog. Windows hides it by default – *Sound settings → More sound settings →
Recording → right click → Show disabled devices → Stereomix → Enable*. Not every sound driver offers it; a virtual
audio cable (a separate program) does the same. With a microphone picked, the room plays the microphone.

**Without HTTPS.** Browsers hand out sound only to pages opened over HTTPS. If ComfyOS has to stay on `http://`,
the browser on the computer can be told to trust it: in Chrome and Edge `chrome://flags` → "Insecure origins treated as
secure" → enter the address of ComfyOS; in Firefox `about:config` → set `media.devices.insecure.enabled` and
`media.getusermedia.insecure.enabled` to `true` (this applies to every unencrypted page, Firefox still asks each time).

How it works: the page turns the sound into plain samples and sends them to ComfyOS (with your login). ComfyOS keeps
nothing and offers them as one endless WAV stream; the plugin tells the speaker the address and the speaker fetches
the stream itself.

**The address without login.** A speaker cannot log in. The stream therefore has a fixed address that answers without
login: `http://<ComfyOS>/stream/live.wav`. It answers only to devices in the home network and only while a transmission
runs; nothing can be sent to it and it never shows anything but the sound. The consequence: **whoever is in your home
network can listen to the sound of your computer while you transmit it.** Nothing is reachable from outside.

## Privacy

- All connections stay in the home network; ComfyOS allows nothing else for this plugin.
- ComfyOS only shows cover pictures if the speaker delivers them itself. Pictures that live at a music service on the
  internet are not loaded – the speaker icon is shown then.
- The plugin reads no credentials of music services.

## Limits

- **UPnP off:** if the switch in the Sonos app is off, the speakers do not answer. The rooms stay visible in ComfyOS
  (offline), the tile names the menu path.
- **New address:** if a speaker gets a new address from the router, "Neu suchen" helps – or a fixed address
  in the router.
- **Favourites:** favourites that carry an address can be started (radio stations, albums, playlists). Links into
  the app of a music service cannot be started locally and are missing in the selection.
- **Stations of the newer TuneIn service:** a speaker refuses their address over the local interface (UPnP error 402).
  The plugin then starts the same station under the number of the old TuneIn service (`soap.legacy_radio_uri`).
- **Sound of a speaker on a PC:** not possible. Speakers do not offer what they play as a stream.
- **Sound of this computer:** confirmed on a Beam with Chrome and Edge – the recommended browsers. The way for
  Firefox (recording device such as "Stereomix") works technically, but Firefox's own restrictions can get in the
  way. The computer keeps playing the sound itself; turn it down there.
- **TV and line-in:** the speaker rejects pause and track change for these sources; ComfyOS reports that.
- Not included: announcements, browsing music services, alarms, home cinema settings, setting up new speakers.

## Files

| File | Content |
|---|---|
| `__init__.py` | plugin: rooms, queries, commands, cover pictures |
| `soap.py` | translation between the UPnP interface and Python values (pure functions) |
| `player.py` | one speaker: sending actions, reading the playback |
| `household.py` | rooms, speakers that belong together, groups |
| `routes.py`, `static/settings.js` | tile |

## Tests

```
python public/plugins/sonos/tests/test_sonos_plugin.py
```

The tests run without ComfyOS and without speakers: a household in memory answers the requests.
