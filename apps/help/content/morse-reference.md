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
