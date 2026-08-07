# Appendix: Morse Code Reference

Every character the station can send and decode — this table is generated from the
same tables the decoder and keyer use (`apps/cw/engine/morse.py`), so what you see
here is exactly what goes on the air. `·` is a dit (one unit), `—` is a dah (three
units); gaps are one unit inside a character, three between characters, seven
between words.

### Letters

| Char | Code | | Char | Code |
|:---:|:---|---|:---:|:---|
| **A** | `.-` · — | | **N** | `-.` — · |
| **B** | `-...` — · · · | | **O** | `---` — — — |
| **C** | `-.-.` — · — · | | **P** | `.--.` · — — · |
| **D** | `-..` — · · | | **Q** | `--.-` — — · — |
| **E** | `.` · | | **R** | `.-.` · — · |
| **F** | `..-.` · · — · | | **S** | `...` · · · |
| **G** | `--.` — — · | | **T** | `-` — |
| **H** | `....` · · · · | | **U** | `..-` · · — |
| **I** | `..` · · | | **V** | `...-` · · · — |
| **J** | `.---` · — — — | | **W** | `.--` · — — |
| **K** | `-.-` — · — | | **X** | `-..-` — · · — |
| **L** | `.-..` · — · · | | **Y** | `-.--` — · — — |
| **M** | `--` — — | | **Z** | `--..` — — · · |

### Numbers

| Char | Code |
|:---:|:---|
| **0** | `-----` — — — — — |
| **1** | `.----` · — — — — |
| **2** | `..---` · · — — — |
| **3** | `...--` · · · — — |
| **4** | `....-` · · · · — |
| **5** | `.....` · · · · · |
| **6** | `-....` — · · · · |
| **7** | `--...` — — · · · |
| **8** | `---..` — — — · · |
| **9** | `----.` — — — — · |

### Punctuation

| Char | Code |
|:---:|:---|
| **.** | `.-.-.-` · — · — · — |
| **,** | `--..--` — — · · — — |
| **?** | `..--..` · · — — · · |
| **'** | `.----.` · — — — — · |
| **!** | `-.-.--` — · — · — — |
| **/** | `-..-.` — · · — · |
| **(** | `-.--.` — · — — · |
| **)** | `-.--.-` — · — — · — |
| **&** | `.-...` · — · · · |
| **:** | `---...` — — — · · · |
| **;** | `-.-.-.` — · — · — · |
| **=** | `-...-` — · · · — |
| **+** | `.-.-.` · — · — · |
| **-** | `-....-` — · · · · — |
| **_** | `..--.-` · · — — · — |
| **"** | `.-..-.` · — · · — · |
| **@** | `.--.-.` · — — · — · |

### Prosigns

Prosigns are procedural signals sent as a single run-together symbol (no gap
between the letters). Type them in the composer as shown — `<AR>` keys as one
character.

| Prosign | Code | Meaning |
|:---:|:---|:---|
| **&lt;AR&gt;** | `.-.-.` · — · — · | End of message |
| **&lt;SK&gt;** | `...-.-` · · · — · — | End of contact — signing off |
| **&lt;BT&gt;** | `-...-` — · · · — | Break / pause (new paragraph) |
| **&lt;KN&gt;** | `-.--.` — · — — · | Go ahead, named station only |
| **&lt;BK&gt;** | `-...-.-` — · · · — · — | Break-in — quick back-and-forth |
| **&lt;AS&gt;** | `.-...` · — · · · | Wait / stand by |
| **&lt;CT&gt;** | `-.-.-` — · — · — | Start of message (also &lt;KA&gt;) |

Notes the decoder lives by:

- `<AR>` and `+` share a code (`.-.-.`), as do `<AS>` and `&` — when decoding, the
  plain character is shown for readability.
- A character the decoder can't match is shown as `▯` with low confidence — on the
  session page those render red so you know what to distrust.
- Speed is measured in **WPM by the PARIS standard**: the word PARIS is exactly
  50 units, so one dit = 1200 ms ÷ WPM. At 20 WPM a dit is 60 ms.

## CW shorthand (tutor mode)

The abbreviations, Q-codes, and signs an operator hears constantly. Toggle **Aa** on the live tape or simulator and the copy's shorthand expands into plain English as it comes in — the same table lives here.

### Q-codes

| | |
|:--|:--|
| **QRL** | is this frequency in use? / I'm busy |
| **QRM** | interference (from other stations) |
| **QRN** | static / natural noise |
| **QRO** | increase power |
| **QRP** | low power operating |
| **QRQ** | send faster |
| **QRS** | send more slowly |
| **QRT** | stop sending / going off the air |
| **QRV** | ready / standing by |
| **QRX** | wait / stand by |
| **QRZ** | who is calling me? |
| **QSB** | your signal is fading |
| **QSL** | acknowledged / I confirm |
| **QSO** | a contact / conversation |
| **QSY** | change frequency |
| **QTH** | location / station address |
| **QTR** | the time |

### Calling & procedure

| | |
|:--|:--|
| **CQ** | calling any station |
| **DE** | from / this is |
| **K** | over — go ahead, anyone |
| **KN** | over — named station only |
| **AR** | end of message |
| **SK** | end of contact — signing off |
| **BK** | break — quick back-and-forth |
| **BT** | separator / new paragraph |
| **AS** | wait / stand by |
| **CT** | start of message |
| **R** | roger — received OK |
| **CL** | closing station |

### Prosigns — as they appear in copy

| | |
|:--|:--|
| `+` | end of message (AR) |
| `=` | separator (BT) — new thought |
| `(` | go ahead — named station only (KN) |
| `&` | wait / stand by (AS) |
| `<SK>` | end of contact — signing off |
| `<BK>` | break-in — over to you |
| `<CT>` | start of message (KA) |

### Common words

| | |
|:--|:--|
| **ABT** | about |
| **AGN** | again |
| **ANT** | antenna |
| **BCNU** | be seeing you |
| **BTU** | back to you |
| **CFM** | confirm |
| **CPY** | copy |
| **CUL** | see you later |
| **CUAGN** | see you again |
| **DR** | dear |
| **DX** | distant / long-distance station |
| **ES** | and |
| **FB** | fine business — excellent |
| **FER** | for |
| **FM** | from |
| **GA** | good afternoon |
| **GB** | goodbye |
| **GE** | good evening |
| **GL** | good luck |
| **GM** | good morning |
| **GN** | good night |
| **GUD** | good |
| **HI** | laughter (ham 'ha ha') |
| **HR** | here |
| **HW** | how do you copy? |
| **MNI** | many |
| **NIL** | nothing / I have nothing for you |
| **NR** | number |
| **NW** | now |
| **OM** | old man — any operator |
| **OP** | operator / name |
| **PSE** | please |
| **PWR** | power |
| **RIG** | radio equipment |
| **RST** | signal report (readability-strength-tone) |
| **SRI** | sorry |
| **TKS** | thanks |
| **TNX** | thanks |
| **TU** | thank you |
| **UR** | your / you are |
| **VY** | very |
| **WID** | with |
| **WKD** | worked |
| **WL** | well / will |
| **WUD** | would |
| **WX** | weather |
| **XYL** | wife |
| **YL** | young lady — female operator |

### Numbers & signs

| | |
|:--|:--|
| **73** | best regards |
| **88** | love and kisses |
| **5NN** | 599 — a perfect report |
