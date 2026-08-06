# Tutorial: Your First Ten Minutes

No radio, no Morse knowledge needed — just the app running (`make run`) and a
logged-in account. Each step takes about a minute.

## 1. Decode your first message

1. Click **CW Monitor → Decode** in the sidebar.
2. In the **Practice** card, type your name into the message box.
3. Leave the sliders alone (20 wpm, 600 Hz) and click **Synthesize & decode**.

You land on a session page: your name as the decoder copied it, with a perfect
accuracy score — the app generated the Morse audio and decoded it back.

## 2. Watch and listen to it

1. On that session page, press the **▶ play** button under the tape.
2. Watch the gold bars (your name in dits and dahs) cross the green **NOW** line —
   each letter resolves the moment the decoder commits it.
3. Click the **🔇 speaker** button so it turns on, press **↺ restart**, and listen
   while you watch. That's what your name sounds like in Morse.
4. Drag **playback** down to 0.5× — slower tape, same pitch. Good for training your ear.

## 3. Hear how noise changes things

1. Go back to **Decode → Practice**, same message.
2. Type `12` into **Add noise (SNR dB)** and decode again.
3. On the session page, replay it: the envelope line over the bars is now shaggy,
   and the copy may have red (low-confidence) characters. That's what a weak
   signal does — and why the receiver controls exist.

## 4. Send something

1. Click **CW Monitor → Send**.
2. Type `/` in the message box — your **message keys** pop up. Pick **/cq**
   (arrow keys + Enter, or click it).
3. The message expands with your call filled in. Click **Key it**.
4. Play the audio on the session page — that's a properly-timed CQ call you
   could put on the air.

## 5. Work a simulated band

1. Click **CW Monitor → Simulator**, and in a terminal run: `PORT=8005 make sim`
   (use the port your server runs on — the setup card on the page shows the
   exact command).
2. Watch the tape: static… then a station appears. Turn the **🔊 sidetone** on and
   listen to the band.
3. Watch the **Tone** gauge jump when a new station calls at a different pitch —
   that's AFC re-locking.
4. Drag **Band static** up until junk characters appear in the decode window, then
   raise **Squelch** until the junk stops. You just tuned a receiver.
5. When a callsign appears in **Heard on the band**, click its **reply** chip —
   the send composer slides up, pre-addressed. Type `/rst`, press Enter, **Key it**.
   Your reply appears in the **Sent** log with a play button.

## 6. Where everything lives

- **CW Monitor** (home) — replay any stored session on the tape.
- **Live** — the same tape fed by a real radio via `cw_monitor_live` (see the
  [Operator's Guide](/smallstack/help/cw-station/)).
- **Sessions** — every decode and send, searchable (try the topbar search).
- [Morse Code Reference](/smallstack/help/morse-reference/) — every character,
  digit, and prosign the station speaks.

That's the whole loop: decode, listen, send, work the band. When a real radio
arrives, the [Operator's Guide](/smallstack/help/cw-station/) covers plugging it
in — and none of what you just learned changes.
