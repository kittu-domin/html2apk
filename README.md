# APK Builder Bot 🤖

A Telegram bot that converts HTML/ZIP files into Android APKs.

## Features
- HTML & ZIP → APK conversion
- Custom app name + icon per build
- Auto APK signing
- Files >50 MB sent via Telethon userbot
- Force join channels
- Broadcast to all users
- Full admin panel via `/admin`

## Setup

### 1. Clone & enter folder
```bash
git clone https://github.com/YOURUSERNAME/apk-builder-bot.git
cd apk-builder-bot
```

### 2. Install tools (Java, apktool, apksigner)
```bash
bash setup_tools.sh
```

### 3. Configure
```bash
cp .env.example .env
nano .env
```

Fill in:
| Key | Where to get |
|-----|-------------|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `OWNER_ID` | [@userinfobot](https://t.me/userinfobot) |
| `API_ID` / `API_HASH` | [my.telegram.org](https://my.telegram.org) |
| `SUPPORT_USERNAME` | Your Telegram username |

### 4. Run
```bash
source venv/bin/activate
python bot.py
```

### 5. (Recommended) Run as systemd service
```bash
sudo cp apk_bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now apk_bot
```

### 6. Add Swap (for 1 GB VPS)
```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Admin Commands
| Command | Action |
|---------|--------|
| `/admin` | Open admin panel |
| `/stats` | Quick stats |
| `/cancel` | Cancel current action |

## File Structure
```
apk-builder-bot/
├── bot.py            # Main bot
├── config.py         # All settings
├── converter.py      # HTML → APK engine
├── userbot.py        # Telethon userbot manager
├── requirements.txt  # Python dependencies
├── setup_tools.sh    # Install Java/apktool/etc
├── apk_bot.service   # systemd service file
├── .env.example      # Config template
└── .gitignore
```
