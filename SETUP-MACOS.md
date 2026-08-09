# Set up CW Station on a MacBook

Step-by-step from a clean macOS machine (zsh, the default shell) to a running CW
Station. Everything runs locally on SQLite — no external services. The dev server
listens on **port 8010**.

## 1. Install Homebrew

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

This also installs Apple's Command Line Tools (which provide `git` and `make`). On
**Apple-Silicon Macs**, add Homebrew to your shell (the installer prints this too):

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

## 2. Update / install Python (3.12+)

```bash
brew install python@3.13
brew upgrade python@3.13        # if it was already installed
python3 --version               # should print 3.12 or newer
```

*(uv can also manage Python itself, so this is mainly to have a modern `python3` on the system.)*

## 3. Install uv (the package manager this project uses)

```bash
brew install uv
uv --version
```

## 4. (Optional) Install Hamlib — only for a real radio

CW Station decodes recordings, runs the practice simulator, and makes WAVs **without**
a radio. Install this only for live CAT rig control:

```bash
brew install hamlib
```

## 5. Get the repository

**If you're just running it (not a collaborator):** clone over HTTPS — no SSH key needed.

```bash
cd ~/Documents            # or wherever you keep projects
git clone https://github.com/emichaud/cwmonitor.git
cd cwmonitor
```

<details>
<summary><b>Collaborators only</b> — clone over SSH so you can push (optional)</summary>

Set up an SSH key once if you don't have one registered with GitHub
(`ssh -T git@github.com` says *"Hi &lt;you&gt;!"* when it's ready):

```bash
ssh-keygen -t ed25519 -C "you@example.com"      # press Enter through the prompts
pbcopy < ~/.ssh/id_ed25519.pub                    # copies the public key
```

Paste it at **GitHub → Settings → SSH and GPG keys → New SSH key**, then:

```bash
ssh -T git@github.com
git clone git@github.com:emichaud/cwmonitor.git
cd cwmonitor
```
</details>

## 6. Initialize the project + create your admin account

Install dependencies and set up the database:

```bash
uv sync --all-extras
uv run python manage.py migrate
```

Create your superuser **in your name**. Tip: use your **callsign** as the username —
CW Station fills `{mycall}` in send macros from it, so keying "just works":

```bash
uv run python manage.py createsuperuser
# Username:  N1KRX        <- your callsign (or your name)
# Email:     you@example.com
# Password:  (choose one)
```

*(Optional)* seed a few sample rig photos for that account so the Rig Setup page has
something to show — replace `N1KRX` with the username you just chose:

```bash
uv run python manage.py cw_demo_photos --user N1KRX
```

## 7. Run it

```bash
make run
```

Open **http://localhost:8010/** and sign in with the account you created. Use a
different port with `PORT=8080 make run`.

---

## Handy commands afterward

```bash
make run       # start the dev server (port 8010)
make test      # run the test suite
make lint      # ruff + the template-comment check
make migrate   # apply new migrations
make backup    # snapshot the SQLite database
```

- **`make setup`** is the all-in-one shortcut (dependencies + migrate + a default
  `admin` / `admin` user + demo photos). The steps above use `createsuperuser` instead so
  your first account is *yours* rather than the generic `admin`. To just kick the tires,
  `make setup && make run` works and logs in with `admin` / `admin`.
- Everything runs locally on **SQLite** with no external services — nothing else to install.
